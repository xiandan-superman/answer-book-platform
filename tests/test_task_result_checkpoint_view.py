from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app import task_result_view

ROOT = Path(__file__).parents[1]


def test_task_result_view_exposes_per_question_checkpoint_plan(tmp_path, monkeypatch) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "structured_exam.json").write_text(
        json.dumps(
            {
                "items": [
                    {"question_id": "q1", "number": "1", "stem": "题一"},
                    {"question_id": "q2", "number": "2", "stem": "题二"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (stage / "answer_fragments.json").write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "number": "1",
                        "answer": "答案一",
                        "blocks": [],
                        "formulas": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (stage / "answer_checkpoint_reconciliation.json").write_text(
        json.dumps(
            {
                "resume_strategy": "reuse_valid_regenerate_missing_or_invalid",
                "reusable_question_ids": ["q1"],
                "redrive_question_ids": ["q2"],
                "source_contract": {"status": "matched"},
                "inconsistencies": [],
            }
        ),
        encoding="utf-8",
    )
    record = SimpleNamespace(status="paused", current_stage="answer_generation", error="")
    monkeypatch.setattr(task_result_view, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(task_result_view, "load_task", lambda _task_id: record)

    report = task_result_view.build_task_result_view("task")

    assert report["metrics"]["checkpoint_reusable_count"] == 1
    assert report["metrics"]["checkpoint_redrive_count"] == 1
    assert [item["checkpoint_status"] for item in report["questions"]] == ["reusable", "redrive"]
    assert report["checkpoint_reconciliation"]["source_contract"]["status"] == "matched"


def test_task_result_view_tolerates_malformed_optional_json(tmp_path, monkeypatch) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "structured_exam.json").write_text("[]", encoding="utf-8")
    (stage / "answer_fragments.json").write_text("{broken", encoding="utf-8")
    record = SimpleNamespace(status="paused", current_stage="answer_generation", error="")
    monkeypatch.setattr(task_result_view, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(task_result_view, "load_task", lambda _task_id: record)

    report = task_result_view.build_task_result_view("task")

    assert report["metrics"]["question_count"] == 0
    assert report["questions"] == []


def test_task_result_view_tolerates_malformed_nested_collections(tmp_path, monkeypatch) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "structured_exam.json").write_text(
        json.dumps({"items": [{"question_id": "q1", "number": "1", "stem": "题一"}]}),
        encoding="utf-8",
    )
    (stage / "answer_fragments.json").write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "blocks": None,
                        "formulas": "invalid",
                        "evidence_ids": "invalid",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (stage / "answer_checkpoint_reconciliation.json").write_text(
        json.dumps(
            {
                "reusable_question_ids": "q1",
                "redrive_question_ids": ["foreign"],
                "source_contract": "invalid",
                "inconsistencies": None,
            }
        ),
        encoding="utf-8",
    )
    record = SimpleNamespace(status="paused", current_stage="answer_generation", error="")
    monkeypatch.setattr(task_result_view, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(task_result_view, "load_task", lambda _task_id: record)

    report = task_result_view.build_task_result_view("task")

    assert report["metrics"]["checkpoint_reusable_count"] == 0
    assert report["metrics"]["checkpoint_redrive_count"] == 0
    assert report["questions"][0]["checkpoint_status"] == "not_evaluated"
    assert report["questions"][0]["blocks"] == []
    assert report["checkpoint_reconciliation"]["source_contract"] == {}
    assert report["checkpoint_reconciliation"]["inconsistencies"] == []


def test_task_result_ui_renders_checkpoint_reuse_and_redrive_status() -> None:
    js = (ROOT / "web/app.js").read_text(encoding="utf-8")
    css = (ROOT / "web/styles.css").read_text(encoding="utf-8")

    assert "function checkpointStatusMeta(status)" in js
    assert '断点复用' in js
    assert '待重跑' in js
    assert "checkpoint_reusable_count" in js
    assert "checkpoint_redrive_count" in js
    assert ".checkpoint-reusable" in css
    assert ".checkpoint-redrive" in css


def test_task_result_view_excludes_duplicate_and_foreign_fragments(tmp_path, monkeypatch) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "structured_exam.json").write_text(
        json.dumps(
            {
                "items": [
                    {"question_id": "q1", "number": "1", "stem": "题一"},
                    {"question_id": "q2", "number": "2", "stem": "题二"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (stage / "answer_fragments.json").write_text(
        json.dumps(
            {
                "fragments": [
                    {"question_id": "q1", "answer": "第一份", "blocks": [], "formulas": []},
                    {"question_id": "q1", "answer": "第二份", "blocks": [], "formulas": []},
                    {"question_id": "foreign", "answer": "外来", "blocks": [], "formulas": []},
                    {"question_id": "q2", "answer": "答案二", "blocks": [], "formulas": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    (stage / "answer_checkpoint_reconciliation.json").write_text(
        json.dumps(
            {
                "reusable_question_ids": ["q1", "q2", "foreign"],
                "redrive_question_ids": ["q1"],
            }
        ),
        encoding="utf-8",
    )
    record = SimpleNamespace(status="paused", current_stage="answer_generation", error="")
    monkeypatch.setattr(task_result_view, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(task_result_view, "load_task", lambda _task_id: record)

    report = task_result_view.build_task_result_view("task")

    assert report["metrics"]["answered_count"] == 1
    assert report["metrics"]["checkpoint_reusable_count"] == 1
    assert report["metrics"]["checkpoint_redrive_count"] == 1
    assert report["questions"][0]["has_answer"] is False
    assert report["questions"][0]["checkpoint_status"] == "redrive"
    assert report["questions"][1]["answer"] == "答案二"
    assert report["questions"][1]["checkpoint_status"] == "reusable"


def test_task_result_view_preserves_formula_boundaries_for_web_math(tmp_path, monkeypatch) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "structured_exam.json").write_text(
        json.dumps({"items": [{"question_id": "q1", "number": "1", "stem": "题一"}]}),
        encoding="utf-8",
    )
    (stage / "answer_fragments.json").write_text(
        json.dumps(
            {
                "fragments": [
                    {
                        "question_id": "q1",
                        "answer": "答案",
                        "blocks": [
                            {
                                "label": "解析",
                                "segments": [
                                    {"type": "text", "text": "由"},
                                    {"type": "formula_ref", "formula_id": "f1"},
                                    {"type": "text", "text": "可得结论。"},
                                ],
                            }
                        ],
                        "formulas": [
                            {"formula_id": "f1", "latex": r"\frac{a}{b}=c"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    record = SimpleNamespace(status="completed", current_stage="completed", error="")
    monkeypatch.setattr(task_result_view, "stage_dir", lambda _task_id: stage)
    monkeypatch.setattr(task_result_view, "load_task", lambda _task_id: record)

    report = task_result_view.build_task_result_view("task")
    analysis = report["questions"][0]["blocks"][0]["text"]

    assert r"\(\frac{a}{b}=c\)" in analysis
    assert r"公式：\frac" not in analysis
