from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .capabilities.catalog import apply_capability_policy_transforms, capability_policy_contributions
from .llm_client import LLMError, OpenAICompatibleClient
from .concurrency import model_request_slot, run_limited_concurrent
from .question_requirements import answer_figure_required
from .question_types import question_has_type
from .runtime_monitor import model_call_context
from .settings import DEFAULT_MODEL_MAX_TOKENS, ProviderConfig, provider_model_supports_vision
from .text_utils import clean_text


COMPLEX_TABLE_MAX_SIMPLE_CELLS = 12
COMPLEX_TABLE_MAX_SIMPLE_COLS = 5
QUESTION_UNDERSTANDING_POLICY_VERSION = "answer_book.question_understanding_policy.v5"


def question_understanding_worker_count() -> int:
    raw = os.environ.get("QUESTION_UNDERSTANDING_MAX_WORKERS", "10")
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 10


@lru_cache(maxsize=64)
def _cached_image_data_url(path_text: str, size: int, modified_ns: int) -> str:
    del size, modified_ns
    path = Path(path_text)
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _image_data_url(path: Path) -> str:
    stat = path.stat()
    return _cached_image_data_url(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def _table_rows(table: dict[str, Any]) -> list[list[str]]:
    rows = table.get("rows") or table.get("table_rows") or []
    out: list[list[str]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, list):
            continue
        clean_row = [clean_text(str(cell)) for cell in row]
        if any(clean_row):
            out.append(clean_row)
    return out


def is_complex_table(table: dict[str, Any]) -> bool:
    rows = _table_rows(table)
    if not rows:
        return False
    col_counts = [len(row) for row in rows]
    max_cols = max(col_counts)
    cell_count = sum(col_counts)
    if len(set(col_counts)) > 1:
        return True
    if max_cols > COMPLEX_TABLE_MAX_SIMPLE_COLS or cell_count > COMPLEX_TABLE_MAX_SIMPLE_CELLS:
        return True
    joined = "\n".join(cell for row in rows for cell in row)
    return bool(re.search(r"[\n\r]|[×·∆Δ_{}^]|[A-Za-z]\s*/|mol-1|s-1|K-1", joined))


def _snapshot_font(size: int):
    try:
        from PIL import ImageFont

        for raw in (
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
        ):
            path = Path(raw)
            if path.exists():
                return ImageFont.truetype(str(path), size)
        return ImageFont.load_default()
    except Exception:
        return None


def render_table_to_image(table: dict[str, Any], output: Path) -> Path:
    rows = _table_rows(table)
    if not rows:
        raise ValueError("table rows are empty")
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        raise RuntimeError("Pillow is required to render table images") from exc

    font = _snapshot_font(24)
    if font is None:
        raise RuntimeError("No usable font for table rendering")
    padding_x = 22
    padding_y = 16
    min_cell_width = 120
    line_height = 34
    columns = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (columns - len(row)) for row in rows]
    scratch = Image.new("RGB", (100, 100), "white")
    draw = ImageDraw.Draw(scratch)
    widths: list[int] = []
    for col in range(columns):
        max_width = min_cell_width
        for row in normalized_rows:
            for line in str(row[col]).splitlines() or [""]:
                try:
                    max_width = max(max_width, int(draw.textlength(line, font=font)) + padding_x * 2)
                except Exception:
                    max_width = max(max_width, len(line) * 18 + padding_x * 2)
        widths.append(min(max_width, 320))
    row_heights = [max(line_height + padding_y * 2, line_height * max(1, max(str(cell).count("\n") + 1 for cell in row)) + padding_y * 2) for row in normalized_rows]
    width = sum(widths) + 2
    height = sum(row_heights) + 2
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    y = 1
    for row_index, row in enumerate(normalized_rows):
        x = 1
        for col_index, cell in enumerate(row):
            fill = "#f8fafc" if row_index == 0 else "#ffffff"
            draw.rectangle((x, y, x + widths[col_index], y + row_heights[row_index]), fill=fill, outline="#334155", width=1)
            text_y = y + padding_y
            for line in str(cell).splitlines() or [""]:
                draw.text((x + padding_x, text_y), line, fill="#0f172a", font=font)
                text_y += line_height
            x += widths[col_index]
        y += row_heights[row_index]
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output


def question_tables(question: dict[str, Any]) -> list[dict[str, Any]]:
    return [table for table in ((question.get("attachments") or {}).get("tables") or []) if isinstance(table, dict)]


def is_drawing_question(question: dict[str, Any]) -> bool:
    return answer_figure_required(question)


def needs_vision_model(question: dict[str, Any]) -> bool:
    if question.get("image_refs"):
        return True
    return any(is_complex_table(table) for table in question_tables(question))


def _local_understanding(question: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    qid = str(question.get("question_id") or "").strip()
    tables: list[dict[str, Any]] = []
    for index, table in enumerate(question_tables(question), start=1):
        rows = _table_rows(table)
        item: dict[str, Any] = {
            "table_id": f"{qid}_table_{index:02d}" if qid else f"table_{index:02d}",
            "table_rows": rows,
            "text": table.get("text") or "",
            "is_complex": is_complex_table(table),
            "uncertainties": [],
        }
        if item["is_complex"]:
            try:
                rendered = render_table_to_image(table, output_dir / "table_renders" / f"{item['table_id']}.png")
                item["table_render"] = str(rendered)
            except Exception as exc:
                item["uncertainties"].append(f"复杂表格渲染失败：{exc}")
        tables.append(item)

    images: list[dict[str, Any]] = []
    for index, raw in enumerate(question.get("image_refs") or [], start=1):
        path = Path(str(raw))
        images.append(
            {
                "image_id": f"{qid}_image_{index:02d}" if qid else f"image_{index:02d}",
                "path": str(path),
                "exists": path.exists(),
                "ocr_text": "",
                "visual_description": "",
                "detected_labels": [],
                "answer_relevant_observations": [],
                "uncertainties": ["未调用视觉模型，已保留原图供后续复核。"],
            }
        )

    return {
        "schema_version": "answer_book.question_understanding.v1",
        "question_id": qid,
        "text": str(question.get("stem") or ""),
        "question_type": question.get("question_type") or "",
        "question_requirements": _question_requirements(question),
        "tables": tables,
        "images": images,
        "needs_vision_model": needs_vision_model(question),
        "needs_figure": is_drawing_question(question),
        "vision_used": False,
        "vision_model": "",
        "uncertainties": [],
    }


def _question_requirements(question: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for sub in question.get("subquestions") or []:
        if not isinstance(sub, dict):
            continue
        requirements = sub.get("requirements")
        if isinstance(requirements, list) and requirements:
            for req in requirements:
                if isinstance(req, dict):
                    rows.append({"number": str(req.get("number") or ""), "type": str(req.get("question_type") or ""), "text": str(req.get("stem") or "")})
            continue
        rows.append({"number": str(sub.get("number") or ""), "type": str(sub.get("question_type") or ""), "text": str(sub.get("stem") or "")})
    if not rows:
        rows.append({"number": str(question.get("number") or ""), "type": str(question.get("question_type") or ""), "text": str(question.get("stem") or "")})
    return rows


def _vision_parts(understanding: dict[str, Any]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for image in understanding.get("images") or []:
        path = Path(str(image.get("path") or ""))
        if path.exists() and path.is_file():
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
    for table in understanding.get("tables") or []:
        path = Path(str(table.get("table_render") or ""))
        if path.exists() and path.is_file():
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
    return parts


def question_visual_parts(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Return each source visual once for direct multimodal delivery."""

    understanding = question.get("question_understanding") if isinstance(question.get("question_understanding"), dict) else {}
    paths: list[Path] = []
    paths.extend(Path(str(raw)) for raw in question.get("image_refs") or [] if str(raw).strip())
    paths.extend(
        Path(str(item.get("path") or ""))
        for item in understanding.get("images") or []
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    )
    paths.extend(
        Path(str(item.get("table_render") or ""))
        for item in understanding.get("tables") or []
        if isinstance(item, dict) and str(item.get("table_render") or "").strip()
    )
    parts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        identity = str(path.resolve())
        if identity in seen:
            continue
        seen.add(identity)
        parts.append({"type": "image_url", "image_url": {"url": _image_data_url(path)}})
    return parts


def attach_question_visuals(messages: list[dict[str, Any]], question: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach original question visuals to the last user message."""

    visual_parts = question_visual_parts(question)
    if not visual_parts:
        return messages
    attached = [dict(message) for message in messages]
    for index in range(len(attached) - 1, -1, -1):
        if attached[index].get("role") != "user":
            continue
        content = attached[index].get("content")
        if isinstance(content, list):
            attached[index]["content"] = [*content, *visual_parts]
        else:
            attached[index]["content"] = [{"type": "text", "text": str(content or "")}, *visual_parts]
        break
    return attached


def _vision_prompt(question: dict[str, Any], understanding: dict[str, Any]) -> list[dict[str, Any]]:
    policy_context = {"question": question, "text": str(understanding.get("text") or "")}
    image_schema: dict[str, Any] = {
        "image_id": "图片 ID",
        "ocr_text": "图片内文字",
        "visual_description": "图像内容说明",
        "detected_labels": ["图中标签"],
        "axes": {"x": "横轴含义", "y": "纵轴含义"},
        "curves": [],
        "data_points": [],
        "answer_relevant_observations": ["作答必须使用的图像信息"],
        "uncertainties": [],
    }
    domain_hard_rules: list[str] = []
    for contribution in capability_policy_contributions(
        "visual_understanding",
        policy_context,
        text=policy_context["text"],
    ):
        if not isinstance(contribution, dict):
            continue
        extension = contribution.get("image_schema")
        if isinstance(extension, dict):
            image_schema.update(extension)
        domain_hard_rules.extend(
            str(rule) for rule in contribution.get("hard_rules", []) if str(rule).strip()
        )
    payload = {
        "task": "understand_exam_question_visual_and_table_content",
        "question_id": understanding.get("question_id"),
        "text": understanding.get("text"),
        "tables": understanding.get("tables"),
        "images": [{"image_id": item.get("image_id"), "path": item.get("path")} for item in understanding.get("images") or []],
        "output_schema": {
            "question_id": understanding.get("question_id"),
            "tables": [
                {
                    "table_id": "表格 ID",
                    "table_rows": [["保留二维数据"]],
                    "visual_notes": "合并单元格、上下标、特殊符号、表头层级等说明",
                    "answer_relevant_observations": ["与作答相关的数据或关系"],
                    "uncertainties": [],
                }
            ],
            "images": [image_schema],
            "question_requirements": understanding.get("question_requirements"),
            "uncertainties": [],
        },
        "hard_rules": [
            "Return exactly one valid JSON object.",
            "Do not solve the problem; only extract visual/table information needed for solving.",
            "Preserve all visible labels, units, axes, legends, and special symbols.",
            "For rendered table images, compare the image with table_rows and note any merged header or symbol nuance.",
            "If information is unreadable, put it in uncertainties instead of guessing.",
            *domain_hard_rules,
        ],
    }
    return [
        {"role": "system", "content": "你是真题题面视觉解析器，只输出 JSON。"},
        {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}, *_vision_parts(understanding)]},
    ]


def _merge_visual_result(base: dict[str, Any], visual: dict[str, Any], provider: ProviderConfig, model: str) -> dict[str, Any]:
    merged = dict(base)
    for key in ("tables", "images", "question_requirements", "uncertainties"):
        if isinstance(visual.get(key), list):
            merged[key] = visual[key]
    merged["vision_used"] = True
    merged["vision_model"] = model
    merged["vision_provider"] = provider.name
    merged["policy_version"] = QUESTION_UNDERSTANDING_POLICY_VERSION
    return apply_capability_policy_transforms(
        "normalize_visual_understanding",
        merged,
        {"text": str(base.get("text") or "")},
        text=str(base.get("text") or ""),
    )


def build_question_understanding(
    question: dict[str, Any],
    output_dir: Path,
    *,
    provider: ProviderConfig | None = None,
    model: str = "",
    client: Any | None = None,
    direct_multimodal: tuple[str, str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = _local_understanding(question, output_dir)
    if not base["needs_vision_model"]:
        return base
    if direct_multimodal:
        direct_provider, direct_model = direct_multimodal
        base["direct_multimodal"] = True
        base["direct_multimodal_provider"] = str(direct_provider or "")
        base["direct_multimodal_model"] = str(direct_model or "")
        base["visual_delivery"] = "direct_with_answer_request"
        for image in base.get("images") or []:
            if isinstance(image, dict):
                image["uncertainties"] = [
                    item for item in image.get("uncertainties", [])
                    if "未调用视觉模型" not in str(item)
                ]
        return base
    if provider is None or not getattr(provider, "api_key", ""):
        base["uncertainties"].append("题目需要视觉模型，但当前未配置 provider 或 API key，使用本地题面结构化兜底。")
        return base
    active_model = str(model or provider.vision_model)
    vision_models = list(
        dict.fromkeys(
            str(candidate).strip()
            for candidate in (
                active_model,
                *(getattr(provider, "vision_model_options", ()) or ()),
                *(getattr(provider, "model_options", ()) or ()),
            )
            if str(candidate).strip() and provider_model_supports_vision(provider, str(candidate).strip())
        )
    )
    if not vision_models:
        base["uncertainties"].append(
            f"题目需要视觉模型，但 {provider.name}/{active_model or '未选择模型'} 未声明图像输入能力。"
        )
        return base
    active_client = client or OpenAICompatibleClient(provider)
    failures: list[str] = []
    for candidate_model in vision_models:
        try:
            with model_request_slot(provider):
                visual = active_client.chat_json_object(
                    _vision_prompt(question, base),
                    model=candidate_model,
                    max_tokens=max(int(provider.max_tokens or DEFAULT_MODEL_MAX_TOKENS), DEFAULT_MODEL_MAX_TOKENS),
                    attempts=1,
                    task_stage="source_analysis",
                    required_evidence_refs=["question_visual"] if needs_vision_model(question) else [],
                    delivered_evidence_refs=["question_visual"],
                    item_ids=[str(question.get("question_id") or question.get("number") or "")],
                    enforce_context_budget=True,
                )
        except Exception as exc:
            failures.append(f"{candidate_model}: {str(exc)[:240]}")
            continue
        if not isinstance(visual, dict):
            failures.append(f"{candidate_model}: 返回非 JSON object")
            continue
        merged = _merge_visual_result(base, visual, provider, candidate_model)
        merged["vision_attempts"] = [
            *({"model": name, "status": "failed", "error": error} for name, error in (
                (item.split(": ", 1)[0], item.split(": ", 1)[1] if ": " in item else item)
                for item in failures
            )),
            {"model": candidate_model, "status": "succeeded"},
        ]
        return merged
    base["vision_attempts"] = [
        {
            "model": item.split(": ", 1)[0],
            "status": "failed",
            "error": item.split(": ", 1)[1] if ": " in item else item,
        }
        for item in failures
    ]
    base["uncertainties"].append(
        "视觉题面解析在有界模型切换后仍失败：" + "；".join(failures)[:900]
    )
    return base


def build_question_understandings(
    structured_exam: dict[str, Any],
    output_json: Path,
    *,
    provider: ProviderConfig | None = None,
    model: str = "",
    progress_json: Path | None = None,
    direct_multimodal: tuple[str, str] | None = None,
) -> dict[str, Any]:
    output_dir = output_json.parent / "question_understanding_assets"
    questions = [question for question in structured_exam.get("items", []) or [] if isinstance(question, dict)]
    progress: dict[str, Any] = {
        "stage": "question_understanding",
        "status": "running",
        "total": len(questions),
        "completed": 0,
        "active": {},
        "recent_events": [],
    }

    def save_progress(event: str, question: dict[str, Any], **detail: Any) -> None:
        if progress_json is None:
            return
        progress["active"] = {
            "question_id": str(question.get("question_id") or ""),
            "number": str(question.get("number") or ""),
            **detail,
        }
        events = list(progress.get("recent_events") or [])
        events.append({"event": event, "question_id": progress["active"]["question_id"], **detail})
        progress["recent_events"] = events[-8:]
        progress_json.parent.mkdir(parents=True, exist_ok=True)
        progress_json.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")

    max_workers = question_understanding_worker_count() if len(questions) > 1 else 1

    def understand_one(question: dict[str, Any]) -> dict[str, Any]:
        active_item = str(question.get("question_id") or question.get("number") or "")
        with model_call_context(stage="question_understanding", active_item=active_item):
            return build_question_understanding(
                question,
                output_dir,
                provider=provider,
                model=model,
                direct_multimodal=direct_multimodal,
            )

    def record_completion(index: int, question: dict[str, Any], understanding: dict[str, Any]) -> None:
        question["question_understanding"] = understanding
        progress["completed"] = int(progress["completed"]) + 1
        save_progress(
            "question_completed",
            question,
            phase=(
                "已配置主模型直接读图"
                if understanding.get("direct_multimodal")
                else "已完成视觉题面判断"
                if understanding.get("vision_used")
                else "已完成题面结构化"
            ),
            needs_vision=bool(understanding.get("needs_vision_model")),
            vision_used=bool(understanding.get("vision_used")),
        )

    for question in questions:
        save_progress("question_started", question, phase="正在整理题干、图片和表格信息")
    items = run_limited_concurrent(questions, understand_one, max_workers=max_workers, on_complete=record_completion)
    report = {
        "schema_version": "answer_book.question_understandings.v1",
        "policy_version": QUESTION_UNDERSTANDING_POLICY_VERSION,
        "question_count": len(items),
        "vision_required_count": sum(1 for item in items if item.get("needs_vision_model")),
        "vision_used_count": sum(1 for item in items if item.get("vision_used")),
        "direct_multimodal_count": sum(1 for item in items if item.get("direct_multimodal")),
        "concurrency": {
            "max_workers": max_workers,
            "parallel_enabled": max_workers > 1 and len(questions) > 1,
        },
        "items": items,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress_json is not None:
        progress["status"] = "completed"
        progress["active"] = {}
        progress_json.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
