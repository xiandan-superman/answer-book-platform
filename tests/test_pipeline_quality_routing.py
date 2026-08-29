from __future__ import annotations

import json
from types import SimpleNamespace

import app.pipeline as pipeline
from app.pipeline import (
    CONTENT_QUALITY_MODEL_REPAIR_CODES,
    _content_repair_touches_drawing_question,
    _governed_content_model_repair_codes,
)


def test_content_model_repair_only_schedules_machine_blocking_rules() -> None:
    allowed = _governed_content_model_repair_codes(CONTENT_QUALITY_MODEL_REPAIR_CODES)

    assert "missing_analysis" in allowed
    assert "missing_answer_unit_content" in allowed
    assert "missing_required_figure" in allowed
    assert "calculation_missing_steps" in allowed
    assert "calculation_missing_substitution" not in allowed
    assert "calculation_answer_missing_unit" not in allowed
    assert "formula_absence_after_retry" not in allowed
    assert "answer_analysis_comparative_contradiction" in allowed
    assert "composition_partition_missing_declared_component" in allowed


def test_text_only_content_repair_reuses_existing_figures() -> None:
    exam = {
        "questions": [
            {"question_id": "q_text", "question_type": "计算题", "stem": "计算"},
            {"question_id": "q_draw", "question_type": "作图题", "stem": "作图"},
            {"question_id": "q_embedded_draw", "question_type": "简答题", "stem": "绘出(112)晶面"},
        ]
    }

    assert not _content_repair_touches_drawing_question(
        {"repaired_question_ids": ["q_text"]}, exam
    )
    assert _content_repair_touches_drawing_question(
        {"repaired_question_ids": ["q_draw"]}, exam
    )

    assert _content_repair_touches_drawing_question(
        {"repaired_question_ids": ["q_draw"]}, {"items": exam["questions"]}
    )
    assert _content_repair_touches_drawing_question(
        {"repaired_question_ids": ["q_embedded_draw"]}, exam
    )


def test_missing_repair_metadata_falls_back_to_safe_figure_refresh() -> None:
    assert _content_repair_touches_drawing_question({}, {"questions": []})


def test_docx_audit_uses_local_repair_before_model_and_stops_when_resolved(
    tmp_path, monkeypatch
) -> None:
    fragments_path = tmp_path / "answer_fragments.json"
    fragments_path.write_text(json.dumps({"fragments": []}), encoding="utf-8")
    audit_results = iter(
        [
            ["Formula-like text must not be written"],
            [],
        ]
    )
    repair_order: list[str] = []

    monkeypatch.setattr(pipeline, "build_docx_from_fragments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "audit_docx_v4", lambda *_args, **_kwargs: next(audit_results))

    def local_repair(*_args, **_kwargs):
        repair_order.append("local")
        return {"ok": True, "changed": True, "repaired_count": 1}

    def unexpected_model_repair(*_args, **_kwargs):
        repair_order.append("model")
        raise AssertionError("model repair must not run after local repair resolves the audit")

    monkeypatch.setattr(pipeline, "repair_answer_fragments_for_docx", local_repair)
    monkeypatch.setattr(pipeline, "repair_fragments_with_model_for_docx", unexpected_model_repair)

    result = pipeline.build_and_audit_docx_with_repair(
        "task-local-first",
        fragments_path,
        tmp_path / "answer.docx",
        tmp_path,
        lambda *_args, **_kwargs: None,
        structured_exam={"questions": []},
        provider=SimpleNamespace(api_key="configured"),
        model="test-model",
        use_model=True,
    )

    assert result["ok"] is True
    assert repair_order == ["local"]
    assert result["repair"]["strategy"] == "local_first_then_bounded_model"
    assert [item["attempt"] for item in result["repair"]["attempts"]] == [
        "initial",
        "after_local_repair",
    ]


def test_docx_local_repair_failure_is_recorded_and_uses_one_bounded_model_fallback(
    tmp_path, monkeypatch
) -> None:
    fragments_path = tmp_path / "answer_fragments.json"
    fragments_path.write_text(json.dumps({"fragments": []}), encoding="utf-8")
    audit_results = iter([["Formula-like text must not be written"], []])
    repair_order: list[str] = []

    monkeypatch.setattr(pipeline, "build_docx_from_fragments", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "audit_docx_v4", lambda *_args, **_kwargs: next(audit_results))

    def failed_local_repair(*_args, **_kwargs):
        repair_order.append("local")
        raise RuntimeError("deterministic repair unavailable")

    def model_repair(*_args, **_kwargs):
        repair_order.append("model")
        return {"ok": True, "changed": True, "repaired_count": 1}

    monkeypatch.setattr(pipeline, "repair_answer_fragments_for_docx", failed_local_repair)
    monkeypatch.setattr(pipeline, "repair_fragments_with_model_for_docx", model_repair)

    result = pipeline.build_and_audit_docx_with_repair(
        "task-local-failure",
        fragments_path,
        tmp_path / "answer.docx",
        tmp_path,
        lambda *_args, **_kwargs: None,
        structured_exam={"questions": []},
        provider=SimpleNamespace(api_key="configured"),
        model="test-model",
        use_model=True,
    )

    assert result["ok"] is True
    assert repair_order == ["local", "model"]
    assert "deterministic repair unavailable" in result["repair"]["local_repair"]["error"]
    assert [item["attempt"] for item in result["repair"]["attempts"]] == [
        "initial",
        "after_model_repair",
    ]
