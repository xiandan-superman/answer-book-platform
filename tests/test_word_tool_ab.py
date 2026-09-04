from __future__ import annotations

import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

from app.document_tool import DocumentToolFailure
from app.docx_audit import audit_docx_v4
from app.officecli_word import OfficeCliPlan, selected_word_tool_variant
from app.practice_export import build_practice_question_docx, validate_docx_output
from app.practice_export_jobs import _cache_key


def test_word_tool_defaults_to_b_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANSWER_BOOK_WORD_TOOL_VARIANT", raising=False)
    assert selected_word_tool_variant() == "B"


def test_word_tool_can_explicitly_switch_to_a(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", "A")
    assert selected_word_tool_variant() == "A"


def test_word_tool_rejects_unknown_variant() -> None:
    with pytest.raises(DocumentToolFailure, match="未知的 Word 工具版本"):
        selected_word_tool_variant("legacy")


def test_practice_export_cache_isolated_between_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"exercises": [{"number": 1, "stem": "题干"}]}
    monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", "A")
    key_a = _cache_key(data)
    monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", "B")
    key_b = _cache_key(data)
    assert key_a != key_b


def test_officecli_plan_uses_native_equation_and_picture_commands(tmp_path: Path) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(b"fixture")
    plan = OfficeCliPlan.new()
    plan.rich_paragraph([("text", "结果为"), ("equation", r"x=\frac{1}{2}")], label="答案")
    plan.picture(image, description="题图")

    equations = [item for item in plan.commands if item.get("type") == "equation"]
    pictures = [item for item in plan.commands if item.get("type") == "picture"]
    assert equations[0]["props"]["formula"] == r"\mathit{x=\frac{1}{2}}"
    assert pictures[0]["props"]["src"] == str(image)
    assert pictures[0]["props"]["alt"] == "题图"


def test_officecli_b_real_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = str(os.environ.get("OFFICECLI_TEST_BINARY") or "").strip()
    if not binary:
        pytest.skip("set OFFICECLI_TEST_BINARY to run the pinned OfficeCLI integration")
    monkeypatch.setenv("OFFICECLI_BINARY", binary)
    monkeypatch.setenv("ANSWER_BOOK_WORD_TOOL_VARIANT", "B")

    from app.docx_v4 import build_docx_from_fragments

    fragments = tmp_path / "answer_fragments.json"
    figure = tmp_path / "figure.png"
    Image.new("RGB", (800, 480), "white").save(figure)
    fragments.write_text(
        json.dumps(
            {
                "document_title": "真题答案解析",
                "fragments": [{
                    "question_id": "q1",
                    "number": "1",
                    "answer_summary": r"结果为 $x=\frac{1}{2}$。",
                    "formulas": [],
                    "blocks": [{
                        "label": "解析",
                        "segments": [
                            {"type": "text", "text": "代入后得到结论。"},
                            {"type": "image_ref", "path": figure.name, "image_id": "figure-1", "alt": "计算示意图"},
                        ],
                    }],
                }],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    answer_docx = tmp_path / "answer.docx"
    build_docx_from_fragments(fragments, answer_docx)
    assert audit_docx_v4(answer_docx) == []
    with ZipFile(answer_docx) as archive:
        assert b"<m:oMath>" in archive.read("word/document.xml")

    practice = {
        "source_analysis": {"subject": "材料科学"},
        "blueprint": {"training_goal": "掌握计算"},
        "exercises": [{
            "number": 1,
            "stem": r"已知 $x=1$，求 $x^2$。",
            "options": [],
            "answer": r"$x^2=1$",
            "solution_steps": [r"代入 $x=1$。"],
            "knowledge_points": ["代数"],
            "formulas": [{"latex": r"E=mc^2", "location": "stem", "role": "given"}],
            "tables": [{"title": "数据表", "location": "stem", "headers": ["量", "值"], "rows": [["x", "1"]]}],
            "figures": [{"figure_id": "g1", "location": "stem", "image_path": str(figure), "description": "计算示意图"}],
        }],
    }
    question_docx = build_practice_question_docx(practice)
    report = validate_docx_output(question_docx, practice)
    assert report["ok"] is True, report
