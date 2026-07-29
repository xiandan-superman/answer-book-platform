from __future__ import annotations

import pytest

from app.exercise_generation import _parse_practice_json, normalize_practice_set


def _exercise(**overrides):
    value = {
        "question_type": "计算题",
        "difficulty": "基础",
        "target_skill": "建立方程",
        "variation_type": "同考点换情境",
        "stem": "已知条件，求未知量。",
        "options": [],
        "answer": "2",
        "solution_steps": ["设未知量。", "列方程并求解。"],
        "knowledge_points": ["一元一次方程"],
        "verification_note": "代入复算成立。",
    }
    value.update(overrides)
    return value


def test_normalize_practice_set_assigns_program_owned_fields():
    raw = {
        "source_analysis": {
            "subject": "数学",
            "question_type": "计算题",
            "knowledge_points": ["方程"],
            "skills": ["建模"],
        },
        "blueprint": {
            "training_goal": "练习根据条件建立方程",
            "progression": ["识别数量关系", "独立建模"],
        },
        "exercises": [_exercise(), _exercise(difficulty="进阶")],
    }

    result = normalize_practice_set(raw, requested_count=2, subject="数学")

    assert result["schema_version"] == "answer_book.practice_set.v1"
    assert [item["exercise_id"] for item in result["exercises"]] == ["practice_01", "practice_02"]
    assert [item["number"] for item in result["exercises"]] == [1, 2]
    assert result["quality"]["status"] == "passed"


def test_normalize_practice_set_reports_incomplete_generation():
    result = normalize_practice_set(
        {"source_analysis": {}, "blueprint": {}, "exercises": [_exercise(solution_steps=[])]},
        requested_count=3,
        subject="物理",
    )

    assert result["quality"]["status"] == "warning"
    assert any("请求生成 3 题" in warning for warning in result["quality"]["warnings"])
    assert any("缺少分步解析" in warning for warning in result["quality"]["warnings"])


def test_normalize_practice_set_rejects_empty_exercises():
    with pytest.raises(ValueError, match="exercises"):
        normalize_practice_set({"exercises": []}, requested_count=3, subject="化学")


def test_practice_parser_accepts_unescaped_newline_from_model():
    content = '{"source_analysis":{"difficulty":"先分析\n再判断"},"exercises":[]}'

    parsed = _parse_practice_json(content)

    assert parsed["source_analysis"]["difficulty"] == "先分析\n再判断"
