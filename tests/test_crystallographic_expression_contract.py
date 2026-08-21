from __future__ import annotations

import json

from app.docx_audit import audit_docx_v4
from app.docx_v4 import _answer_summary_formula_candidates, build_docx_from_fragments
from app.expression_promotion import promote_inline_mathematical_expressions
from app.figures import (
    _image_model_fallback_allowed_for_question,
    _visual_qa_failed_targets,
    normalize_figure_spec,
    program_check_figure_spec,
)
from app.fragment_repair import repair_answer_fragments_for_docx
from app.pipeline import figure_visual_qa_blocking_findings, required_visual_understanding_failures
from app.v4_schema import validate_v4_answer_fragment


def _fragment(text: str, *, answer_summary: str = "见解析") -> dict:
    return {
        "schema_version": "answer_book.answer_fragment.v4",
        "question_id": "q_crystal",
        "section": "一、作图题",
        "question_type": "作图题",
        "number": "1",
        "answer": "见解析",
        "answer_summary": answer_summary,
        "evidence_ids": [],
        "blocks": [{"label": "解析", "segments": [{"type": "text", "text": text}]}],
        "formulas": [],
        "figure_specs": [],
        "warnings": [],
    }


def test_zone_axis_parser_accepts_prompt_contract_overbar_notation() -> None:
    spec = normalize_figure_spec(
        {
            "kind": "zone_axis_diffraction",
            "caption": "bcc [110]",
            "zone_axis": "[110]",
            "lattice": "bcc",
            "label_indices": ["(000)", r"({1\bar{1}0})", r"({1\bar{1}2})", r"({2\bar{2}0})"],
        }
    )

    assert spec["label_indices"] == [[0, 0, 0], [1, -1, 0], [1, -1, 2], [2, -2, 0]]
    assert program_check_figure_spec(spec) == []


def test_crystallographic_overbars_are_promoted_to_formula_objects() -> None:
    fragment = promote_inline_mathematical_expressions(
        _fragment(r"所以斑点指数取(h\bar{h}l)，例如({1\bar{1}2})。")
    )

    assert validate_v4_answer_fragment(fragment) == []
    formula_latex = [item["latex"] for item in fragment["formulas"]]
    assert r"(h\bar{h}l)" in formula_latex
    assert r"(1\bar{1}2)" in formula_latex
    assert sum(segment.get("type") == "formula_ref" for segment in fragment["blocks"][0]["segments"]) == 2


def test_answer_summary_renders_crystallographic_overbar_as_omml(tmp_path) -> None:
    summary = r"允许斑点为(000)、({1\bar{1}0})和({1\bar{1}2})。"
    candidates = _answer_summary_formula_candidates(summary)
    assert [latex for _start, _end, latex in candidates] == [r"(1\bar{1}0)", r"(1\bar{1}2)"]

    fragments = tmp_path / "fragments.json"
    output = tmp_path / "answer.docx"
    fragments.write_text(
        json.dumps(
            {"fragments": [_fragment(r"斑点应取(h\bar{h}l)，例如({1\bar{1}2})。", answer_summary=summary)]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    build_docx_from_fragments(fragments, output)

    assert not any("raw latex" in issue for issue in audit_docx_v4(output))


def test_answer_summary_renders_parenthesized_partial_derivative_as_one_formula(tmp_path) -> None:
    summary = "(1)升高；(2)(∂(ΔG_m)/∂p)_T 小于零。"
    candidates = _answer_summary_formula_candidates(summary)
    partials = [latex for _start, _end, latex in candidates if r"\partial" in latex]
    assert partials == [r"\left(\frac{\partial \Delta G_{m}}{\partial p}\right)_{T}"]

    fragments = tmp_path / "partial_fragments.json"
    output = tmp_path / "partial_answer.docx"
    fragments.write_text(
        json.dumps({"fragments": [_fragment("根据热力学关系判断。", answer_summary=summary)]}, ensure_ascii=False),
        encoding="utf-8",
    )
    build_docx_from_fragments(fragments, output)
    assert output.exists()


def test_registered_programmatic_schema_cannot_silently_use_image_fallback() -> None:
    programmatic = {
        "figure_schema_plan": {
            "render_decision": {"strategy": "programmatic_renderer", "fallback_allowed": True}
        }
    }
    open_ended = {
        "figure_schema_plan": {
            "render_decision": {"strategy": "image_model_fallback", "fallback_allowed": True}
        }
    }

    assert not _image_model_fallback_allowed_for_question(programmatic)
    assert _image_model_fallback_allowed_for_question(open_ended)


def test_visual_transport_failure_blocks_but_does_not_trigger_figure_rewrite(tmp_path) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(b"not-used-by-transport-check")
    report = {
        "enabled": True,
        "items": [
            {
                "question_id": "q1",
                "figure_id": "fig1",
                "path": str(image),
                "qa": {"ok": False, "error": "Provider request failed: timed out"},
            }
        ],
    }
    specs = [{"question_id": "q1", "figure_id": "fig1", "kind": "custom_diagram"}]

    assert _visual_qa_failed_targets(report, specs) == []
    findings = figure_visual_qa_blocking_findings(report)
    assert len(findings) == 1
    assert findings[0]["reason"].startswith("figure visual QA unavailable:")


def test_required_visual_understanding_cannot_continue_without_visual_result() -> None:
    failures = required_visual_understanding_failures(
        {
            "items": [
                {
                    "question_id": "q_image",
                    "needs_vision_model": True,
                    "vision_used": False,
                    "uncertainties": ["视觉题面解析失败：timed out"],
                },
                {"question_id": "q_text", "needs_vision_model": False, "vision_used": False},
            ]
        }
    )

    assert failures == [{"question_id": "q_image", "reason": "视觉题面解析失败：timed out"}]


def test_docx_local_repair_persists_safe_partial_changes(tmp_path) -> None:
    fragments = tmp_path / "fragments.json"
    payload = {"fragments": [_fragment(r"已知 x=1，但 \unknown{x} 仍需复核。")]}
    fragments.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = repair_answer_fragments_for_docx(fragments)
    stored = json.loads(fragments.read_text(encoding="utf-8"))["fragments"][0]

    assert report["changed"] is True
    assert report["ok"] is False
    assert any(segment.get("type") == "formula_ref" for segment in stored["blocks"][0]["segments"])
    assert any(r"\unknown" in segment.get("text", "") for segment in stored["blocks"][0]["segments"])
