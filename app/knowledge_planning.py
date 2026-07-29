from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .concurrency import run_limited_concurrent
from .llm_client import LLMError, OpenAICompatibleClient
from .settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig
from .text_utils import clean_text, tokenize_zh_en


SCHEMA_VERSION = "answer_book.knowledge_plans.v1"


@dataclass
class KnowledgePlanResult:
    ok: bool
    question_count: int
    plan_count: int
    issue_count: int
    output_json: str
    max_workers: int = 1
    parallel_enabled: bool = False


def knowledge_planning_worker_count() -> int:
    raw = os.environ.get("KNOWLEDGE_PLANNING_MAX_WORKERS") or os.environ.get("ANSWER_GENERATION_MAX_WORKERS", "5")
    try:
        return max(1, min(6, int(raw)))
    except ValueError:
        return 5


def knowledge_planning_timeout_seconds() -> int:
    raw = os.environ.get("KNOWLEDGE_PLANNING_TIMEOUT_SECONDS", "60")
    try:
        return max(15, min(180, int(raw)))
    except ValueError:
        return 60


def _write_progress(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _strings(value: Any, limit: int = 12) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [str(x) for x in value]
    else:
        values = []
    out: list[str] = []
    for item in values:
        text = clean_text(item)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def fallback_knowledge_plan(question: dict[str, Any], reason: str = "") -> dict[str, Any]:
    stem = str(question.get("stem", ""))
    tokens = tokenize_zh_en(" ".join([str(question.get("section", "")), stem]))
    key_terms = []
    for token in tokens:
        if len(token) < 2:
            continue
        if token not in key_terms:
            key_terms.append(token)
        if len(key_terms) >= 12:
            break
    plan = {
        "question_id": str(question.get("question_id", "")),
        "knowledge_points": key_terms[:6],
        "formulas": [],
        "key_terms": key_terms,
        "search_queries": [" ".join(key_terms[:8])] if key_terms else [stem[:80]],
        "warnings": [],
    }
    if reason:
        plan["warnings"].append(reason)
    return plan


def normalize_knowledge_plan(question: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    qid = str(question.get("question_id", "")).strip()
    plan = {
        "question_id": qid,
        "knowledge_points": _strings(data.get("knowledge_points")),
        "formulas": _strings(data.get("formulas")),
        "key_terms": _strings(data.get("key_terms")),
        "search_queries": _strings(data.get("search_queries"), limit=6),
        "warnings": _strings(data.get("warnings"), limit=10),
    }
    if not (plan["knowledge_points"] or plan["formulas"] or plan["key_terms"] or plan["search_queries"]):
        return fallback_knowledge_plan(question, "模型未给出可用考查内容，程序退回到题干关键词。")
    if not plan["search_queries"]:
        parts = plan["knowledge_points"] + plan["formulas"] + plan["key_terms"]
        plan["search_queries"] = [" ".join(parts[:12])]
    return plan


def build_knowledge_plan_prompt(question: dict[str, Any]) -> list[dict[str, str]]:
    understanding = question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
    payload = {
        "task": "solve_question_for_textbook_retrieval_intent",
        "hard_rules": [
            "Only return one valid JSON object.",
            "Do not return Markdown.",
            "Do not cite textbooks.",
            "Do not write the final answer explanation.",
            "First identify what knowledge point, theorem, concept, formula, law, or method the question is testing.",
            "If question_understanding is present, treat it as the normalized visual/table surface of the question.",
            "For image/table questions, use question_understanding OCR, visual labels, axes, legends, table_rows, and answer_relevant_observations when identifying tested content.",
            "Do not ignore visual/table observations just because they do not appear in the original stem text.",
            "Use the identified tested content to produce textbook search terms.",
            "Do not simply copy superficial words from the stem if they are not the tested content.",
        ],
        "output_schema": {
            "question_id": question.get("question_id", ""),
            "knowledge_points": ["考查的知识点或方法"],
            "formulas": ["用于定位教材的公式名或公式表达，不确定可为空"],
            "key_terms": ["教材中可能出现的术语"],
            "search_queries": ["用于检索教材索引的短查询"],
            "warnings": [],
        },
        "question": question,
        "question_understanding": understanding,
    }
    return [
        {"role": "system", "content": "你是真题解析平台的考点定位器。你先判断题目考查内容，只输出一个合法 JSON object。不要输出 Markdown 或 JSON 之外的文字。"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def generate_knowledge_plans(
    structured_exam: dict[str, Any],
    provider: ProviderConfig,
    model: str,
    output_json: Path,
    use_model: bool = True,
    progress_json: Path | None = None,
) -> KnowledgePlanResult:
    questions = list(structured_exam.get("items", []))
    max_workers = knowledge_planning_worker_count() if use_model and provider.api_key else 1
    timeout = knowledge_planning_timeout_seconds()
    progress_lock = threading.Lock()
    progress_payload: dict[str, Any] = {
        "stage": "knowledge_planning",
        "provider": provider.name,
        "model": model,
        "status": "running",
        "total": len(questions),
        "completed": 0,
        "failed": 0,
        "max_workers": max_workers,
        "parallel_enabled": max_workers > 1 and len(questions) > 1,
        "timeout_seconds": timeout,
        "items": [],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_progress(progress_json, progress_payload)

    def update_progress(question_id: str, status: str, detail: dict[str, Any] | None = None) -> None:
        if progress_json is None:
            return
        with progress_lock:
            items = [item for item in progress_payload["items"] if item.get("question_id") != question_id]
            items.append(
                {
                    "question_id": question_id,
                    "status": status,
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    **(detail or {}),
                }
            )
            progress_payload["items"] = items
            progress_payload["completed"] = sum(1 for item in items if item.get("status") in {"passed", "fallback"})
            progress_payload["failed"] = sum(1 for item in items if item.get("status") == "fallback")
            progress_payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _write_progress(progress_json, progress_payload)

    def plan_one(question: dict[str, Any]) -> dict[str, Any]:
        qid = str(question.get("question_id", "")).strip()
        update_progress(qid, "running")
        if not use_model or not provider.api_key:
            return {
                "question_id": qid,
                "plan": fallback_knowledge_plan(question, "未调用模型，程序使用题干关键词生成检索计划。"),
                "issues": [],
                "token_feedback": [],
                "status": "fallback",
            }
        client = OpenAICompatibleClient(provider)
        try:
            data = client.chat_json_object(
                build_knowledge_plan_prompt(question),
                model=model,
                max_tokens=DEFAULT_MODEL_MAX_TOKENS,
                timeout=timeout,
                attempts=1,
            )
            report = getattr(client, "last_json_retry_report", {})
            feedback = []
            if report.get("attempts"):
                feedback.append({"question_id": qid, "stage": "knowledge_planning", **report})
            return {
                "question_id": qid,
                "plan": normalize_knowledge_plan(question, data),
                "issues": [],
                "token_feedback": feedback,
                "status": "passed",
            }
        except (LLMError, Exception) as exc:
            report = getattr(client, "last_json_retry_report", {})
            feedback = []
            if report.get("attempts"):
                feedback.append({"question_id": qid, "stage": "knowledge_planning", **report})
            return {
                "question_id": qid,
                "plan": fallback_knowledge_plan(question, "考点定位模型失败，程序退回到题干关键词。"),
                "issues": [{"question_id": qid, "issues": [str(exc)]}],
                "token_feedback": feedback,
                "status": "fallback",
                "error": str(exc),
            }

    def on_complete(index: int, question: dict[str, Any], result: dict[str, Any]) -> None:
        detail = {}
        if result.get("error"):
            detail["error"] = str(result.get("error"))[:500]
        update_progress(str(result.get("question_id") or question.get("question_id") or ""), str(result.get("status") or "passed"), detail)

    results = run_limited_concurrent(questions, plan_one, max_workers=max_workers, on_complete=on_complete)
    plans = [item["plan"] for item in results]
    issues: list[dict[str, Any]] = []
    token_feedback: list[dict[str, Any]] = []
    for item in results:
        issues.extend(item.get("issues") or [])
        token_feedback.extend(item.get("token_feedback") or [])
    output = {
        "schema_version": SCHEMA_VERSION,
        "provider": provider.name,
        "model": model,
        "plans": plans,
        "issues": issues,
        "model_token_feedback": token_feedback,
        "concurrency": {
            "max_workers": max_workers,
            "parallel_enabled": max_workers > 1 and len(questions) > 1,
            "timeout_seconds": timeout,
        },
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    progress_payload["status"] = "completed"
    progress_payload["completed"] = len(plans)
    progress_payload["failed"] = len(issues)
    progress_payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_progress(progress_json, progress_payload)
    return KnowledgePlanResult(
        ok=not issues or len(plans) == len(questions),
        question_count=len(questions),
        plan_count=len(plans),
        issue_count=sum(len(x.get("issues", [])) for x in issues),
        output_json=str(output_json),
        max_workers=max_workers,
        parallel_enabled=max_workers > 1 and len(questions) > 1,
    )


def load_knowledge_plans(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for plan in data.get("plans", []):
        qid = str(plan.get("question_id", "")).strip()
        if qid:
            out[qid] = plan
    return out
