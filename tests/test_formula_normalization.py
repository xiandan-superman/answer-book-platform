import copy
import json

import pytest

from app.answer_generation import _draft_formulas, fragment_from_analysis_draft
from app.formula_normalization import normalize_formula_entry
from app.fragment_repair import _remove_unresolved_formula_placeholders


def test_positional_formula_recovery_preserves_content_and_reference_order():
    equations = [r"\gamma=\sigma J", r"J=J_0+t/\eta", r"\tau=\eta/E"]
    draft = {
        "question_id": "q1",
        "answer": "见解析",
        "analysis_segments": [{"text": "模型为{f1}，迟滞时间为{f3}，柔量为{f2}。", "formula_indices": [1, 3, 2]}],
        "formulas": [{f"f{i}": equation, "meaning": "关系"} for i, equation in enumerate(equations, 1)],
    }
    original = copy.deepcopy(draft)
    fragment = fragment_from_analysis_draft(draft, {"question_id": "q1", "stem": "说明模型", "question_type": "简答题"}, [])
    assert [item["latex"] for item in fragment["formulas"]] == equations
    refs = [segment["formula_id"] for block in fragment["blocks"] for segment in block.get("segments", []) if segment.get("type") == "formula_ref"]
    assert refs == ["f_q1_01", "f_q1_03", "f_q1_02"]
    assert draft == original


@pytest.mark.parametrize("raw", [
    {"f1": "x", "f2": "x"}, {"f2": "x"}, {"f01": "x"},
    {"f1": "x", "latex": "y"}, {"f1": "x", "latex": ""},
    {"f1": 1}, {"f1": " "},
    {"f1": "x", "formula_id": "f2"},
])
def test_ambiguous_formula_is_rejected_without_mutating_candidate(raw):
    original = copy.deepcopy(raw)
    with pytest.raises(ValueError):
        _draft_formulas({"formulas": [raw]}, "q1")
    assert raw == original


def test_canonical_formula_and_matching_duplicate_remain_lossless():
    raw = {"latex": "x=y", "meaning": "关系", "display": False}
    assert normalize_formula_entry(raw, index=1) == raw
    assert normalize_formula_entry({**raw, "f1": "x=y"}, index=1) == raw


def test_unresolved_formula_keeps_visible_diagnostic_and_review_flag():
    fragment = _remove_unresolved_formula_placeholders({"answer": "由{f2}可得。", "blocks": []})
    assert fragment["answer"] == "由【公式缺失：f2】可得。"
    assert fragment["_review_flags"][0]["code"] == "unresolved_formula_reference_removed"
    assert _remove_unresolved_formula_placeholders(copy.deepcopy(fragment)) == fragment


def test_missing_formula_cannot_become_formally_accepted(tmp_path):
    from app.final_acceptance import AUDIT_FILES, build_final_acceptance_report

    stage = tmp_path / "stage"
    output = tmp_path / "output"
    stage.mkdir()
    output.mkdir()
    for name, filename in AUDIT_FILES.items():
        if name == "render":
            continue
        report = {"ok": True, "issues": [], "warnings": []}
        if name == "environment":
            report["formula_conversion"] = {"preferred_chain_ready": True}
        (stage / filename).write_text(json.dumps(report))
    (stage / "acceptance_report.json").write_text(json.dumps({"status": "passed"}))
    fragment = _remove_unresolved_formula_placeholders({"question_id": "q1", "answer": "由{f2}可得。", "blocks": []})
    (stage / "answer_fragments.json").write_text(json.dumps({"provider": "configured", "fragments": [fragment]}))
    (stage / "structured_exam.json").write_text(json.dumps({"items": []}))
    (output / "answer_book.docx").write_bytes(b"candidate")
    report = build_final_acceptance_report(stage, output, require_render=False)
    assert report["formal_acceptance_passed"] is False
    assert report["delivery_tier"] == "review_candidate"
