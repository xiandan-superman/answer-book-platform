"""Read-only, evidence-backed failure messages for current and historical exams."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import TASKS_DIR


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def exam_failure_summary(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("status") != "failed":
        return None
    error = str(row.get("error") or "")
    if "[WinError 5]" in error and "answer_generation_progress.json" in error:
        return {
            "kind": "progress_save_failed", "title": "答案生成进度保存失败",
            "message": "任务已进入答案生成阶段，保存进度时被 Windows 拒绝访问。现有日志不能确定是文件占用还是权限限制。",
            "retry_hint": "请先确认文件占用或权限问题已解除，再从已保存内容重试；无需重新上传材料。",
            "responsibility": "local_environment", "developer_report_required": False,
        }
    if row.get("current_stage") != "final_acceptance":
        return None
    task_id = str(row.get("task_id") or "")
    if not task_id or task_id in {".", ".."} or "/" in task_id or "\\" in task_id:
        return None
    stage = TASKS_DIR / task_id / "stage_outputs"
    report = _read(stage / "final_acceptance_report.json")
    failures = [item for item in (report.get("figure_delivery_summary") or {}).get("items", [])
                if isinstance(item, dict) and item.get("issues") and item.get("answer_required")]
    if not failures:
        return None
    exam = _read(stage / "structured_exam.json")
    questions = {str(item.get("question_id") or ""): item for item in exam.get("questions", []) if isinstance(item, dict)}
    current = {str(item.get("question_id") or ""): item for item in _read(stage / "answer_fragments.json").get("fragments", []) if isinstance(item, dict)}
    before = {str(item.get("question_id") or ""): item for item in _read(stage / "answer_fragments.before_academic_expression_model_repair.json").get("fragments", []) if isinstance(item, dict)}

    def has_image(fragment: dict[str, Any]) -> bool:
        return any(segment.get("type") == "image_ref" and segment.get("role") == "answer_generated_figure"
                   for block in fragment.get("blocks", []) if isinstance(block, dict)
                   for segment in block.get("segments", []) if isinstance(segment, dict))

    labels = []
    lost = False
    for item in failures:
        qid = str(item.get("question_id") or "")
        question = questions.get(qid) or current.get(qid) or {}
        number = str(question.get("display_number") or question.get("number") or "")
        section = str(question.get("section") or "")
        labels.append(f"{section}第{number}题" if number else "作图题")
        lost = lost or (has_image(before.get(qid, {})) and not has_image(current.get(qid, {})))
    affected = "、".join(dict.fromkeys(labels))
    return {
        "kind": "answer_figure_lost" if lost else "answer_figure_missing",
        "title": "答案配图缺失，未通过最终验收",
        "message": f"{affected}的配图未进入最终答案。" + (
            "修复公式前已有插图，修复后插图引用丢失，属于程序处理问题。" if lost else "请检查该题的图片生成与插入结果。"
        ) + ("Word 已生成，但不能作为完整结果交付。" if (report.get("outputs") or {}).get("docx_exists") else "当前结果不能正式交付。"),
        "retry_hint": "请先更新到已修复的程序，再从已保存内容恢复并重新验收；直接重复运行旧程序可能再次失败。" if lost else "补齐缺失配图后重新生成文档并验收；无需重新上传材料。",
        "responsibility": "platform_defect" if lost else "platform_unknown",
        "developer_report_required": lost,
    }
